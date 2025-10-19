package tema1.t03;

import java.util.Scanner;

public class ej05 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Introduce el primer caracter");
        char car1 = sc.next().charAt(0);
        System.out.println("Introduce el segundo caracter");
        char car2 = sc.next().charAt(0);

        System.out.println((car1 == car2) ? "Los caracteres son iguales." : "Los caracteres son diferentes.");
    }
}
