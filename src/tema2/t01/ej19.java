package tema2.t01;

import java.util.Scanner;

// Escribir un programa en Java que lea un número entero por el teclado e imprima todos los números impares menores que él.
public class ej19 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Digite un numero:");
        int num = sc.nextInt();

        for (int i = 1; i <= num; i++) {
            if (i % 2 != 0){
                System.out.println(i);
            }
        }
    }
}
