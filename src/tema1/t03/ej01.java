package tema1.t03;

import java.util.Scanner;

public class ej01 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        System.out.println("Digite un numero entero:");
        int numero = sc.nextInt();

        System.out.println(numero + " es " + ((numero % 2 == 0) ? "par" : "impar"));
    }
}
