package tema1.t03;

import java.util.Scanner;

public class ej08 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Introduce un año");
        int a = sc.nextInt();

        System.out.println((a % 4 == 0 && a % 100 != 0) || (a % 400 == 0) ? "El año es bisiesto." : "El año no es bisiesto.");
    }
}
